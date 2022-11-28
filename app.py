import os

from celery.result import AsyncResult
from celery import Celery

from sqlalchemy import Column, Integer, String, DateTime, create_engine, func, ForeignKey
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.ext.declarative import declarative_base

from flask import Flask, request, jsonify
from flask.views import MethodView
from flask_mail import Message, Mail

from dotenv import load_dotenv


load_dotenv()

app = Flask('Adv')

app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 465
app.config['MAIL_USERNAME'] = os.getenv("GMAIL")
app.config['MAIL_PASSWORD'] = os.getenv("GMAIL_APP_PASS")
app.config['MAIL_USE_TLS'] = False
app.config['MAIL_USE_SSL'] = True
mail = Mail(app)


def get_task(task_id: str) -> AsyncResult:
    return AsyncResult(task_id, app=celery_app)


celery_app = Celery(
    "app",
    backend="redis://redis:6379/1",
    broker="redis://redis:6379/0"
)


@celery_app.task
def send_mail(title, sender, recipients, text):
    msg = Message(
        title,
        sender=sender,
        recipients=recipients
    )
    msg.body = text
    mail.send(msg)
    return {"message": f"mail to {', '.join(recipients)} sent"}


def response_no_access():
    answer = jsonify({"access": "not allowed"})
    answer.status_code = 403
    return answer


def response_not_found(item):
    answer = jsonify({'message': f'{item} not found'})
    answer.status_code = 404
    return answer


class ContextTask(celery_app.Task):
    def __call__(self, *args, **kwargs):
        with app.app_context():
            return self.run(*args, **kwargs)


celery_app.Task = ContextTask

DSN = 'postgresql://app:1234@db:5432/data'
engine = create_engine(DSN)
Base = declarative_base()
Session = sessionmaker(bind=engine)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    name = Column(String(32), unique=True)
    password = Column(String, nullable=False)
    email = Column(String(32), nullable=False)
    token = Column(String, nullable=True)


class Advertisement(Base):
    __tablename__ = "advertisement"

    id = Column(Integer, primary_key=True)
    header = Column(String(32), nullable=False)
    description = Column(String(32), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    owner_id = Column(Integer, ForeignKey("users.id"))
    owner = relationship("User", backref="advertisements")


def get_token(request):
    token = request.headers.environ.get('HTTP_AUTHORIZATION', None)
    if token is None:
        return response_no_access()
    return token


class AdvertisementView(MethodView):
    def get(self, adv_id=''):
        with Session() as session:
            if adv_id:
                advertisement = session.get(Advertisement, adv_id)
                if advertisement is None:
                    answer = response_not_found("advertisement")
                else:
                    answer = {
                        'id': advertisement.id,
                        'header': advertisement.header,
                        'description': advertisement.description,
                        'created_at': advertisement.created_at.isoformat(),
                        'owner_id': advertisement.owner_id
                    }
            else:
                advertisements = session.query(Advertisement).all()
                answer = {}
                if advertisements:
                    for num, adv in enumerate(advertisements):
                        answer[f'{num}'] = {
                            'id': adv.id,
                            'header': adv.header,
                            'description': adv.description,
                            'created_at': adv.created_at.isoformat(),
                            'owner_id': adv.owner_id
                        }
                answer = jsonify({
                    'response': {'count': len(advertisements), 'items': answer}
                })
            return answer

    def post(self):
        load_data = request.json
        token = get_token(request)
        with Session() as session:
            user = session.query(User).filter_by(token=token).first()
            if user is None:
                return response_not_found("user")
            load_data["owner_id"] = user.id
            # load_data["owner"] = user
            new_adv = Advertisement(**load_data)
            new_adv.owner = user
            session.add(new_adv)
            session.commit()
            answer = jsonify({'cereated advertisement': load_data})
            answer.status_code = 201
            return answer

    def patch(self, adv_id):
        updated_data = request.json
        token = get_token(request)
        if not token:
            return response_no_access()
        with Session() as session:
            user = session.query(User).filter_by(token=token).first()
            adv = session.get(Advertisement, adv_id)
            if user is None:
                return response_no_access()
            if adv is None:
                return response_not_found("advertisement")
            if adv.owner != user:
                return response_no_access()
            for key, value in updated_data.items():
                setattr(adv, key, value)
            session.add(adv)
            session.commit()
            return jsonify({'message': f"advertisement {adv_id} updated"})

    def delete(self, adv_id):
        token = get_token(request)
        if token is None:
            return response_no_access()
        with Session() as session:
            user = session.query(User).filter_by(token=token).first()
            adv = session.get(Advertisement, adv_id)
            if adv is None:
                return response_not_found("advertisement")
            if user is None:
                return response_no_access()
            if adv.owner != user:
                return response_no_access()
            session.delete(adv)
            session.commit()
            answer = jsonify({'message': f'advertisement {adv_id} deleted'})
            answer.status_code = 204
            return answer


class UserView(MethodView):
    def post(self):
        user_data = request.json
        with Session() as session:
            new_user = User(**user_data)
            session.add(new_user)
            session.commit()
            answer = jsonify({"add new user": new_user.id})
            answer.status_code = 201
            return answer

    def patch(self, user_id):
        with Session() as session:
            user_id = int(user_id)
            user = session.get(User, user_id)
            if user is None:
                return response_not_found(f'user {user_id}')
            login_data = request.json
            if user.name == login_data["name"] and user.password == login_data["password"]:
                token = f"token_user_{user_id}"
                user.token = token
                session.add(user)
                session.commit()
                return jsonify({"access": "OK", "token": token})
            else:
                answer = jsonify({"message": "invalid login data"})
                answer.status_code = 401
                return answer


class MailView(MethodView):
    def post(self):
        param = request.args.get("min_adv")
        if param:
            param = int(param)
        with Session() as session:
            result = {}
            if param:
                user_list = session.query(User).all()
                user_list = [user for user in user_list if len(user.advertisements) >= param]
            else:
                user_list = session.query(User).all()
            for user in user_list:
                text = f"Hello {user.name}!\nMail sent from Flask-Mail"
                task = send_mail.delay(
                    title=f"Mail for {user.name}",
                    sender=os.getenv("GMAIL"),
                    recipients=[user.email],
                    text=text)
                result[f"{user.name} task_id "] = task.id
            answer = jsonify({"tasks": result})
            answer.status_code = 201
            return answer

    def get(self):
        task_id = request.args.get("task_id")
        task = get_task(task_id)
        return jsonify({"status": task.status, "result": task.result})


app.add_url_rule(
    "/api/v1/adv/<int:adv_id>/",
    view_func=AdvertisementView.as_view("get_adv"),
    methods=["GET", "PATCH", "DELETE"]
)
app.add_url_rule(
    "/api/v1/adv/",
    view_func=AdvertisementView.as_view("register_adv"),
    methods=["GET", "POST"]
)
app.add_url_rule(
    "/api/v1/user/",
    view_func=UserView.as_view("register_user"),
    methods=["POST"]
)
app.add_url_rule(
    "/api/v1/user/<int:user_id>/",
    view_func=UserView.as_view("login_user"),
    methods=["PATCH"]
)
app.add_url_rule(
    "/api/v1/mail/",
    view_func=MailView.as_view("create_task"),
    methods=["POST", "GET"]
)
app.add_url_rule(
    "/api/v1/mail/<task_id>/",
    view_func=MailView.as_view("get_status_task"),
    methods=["GET"]
)

if __name__ == "__main__":
    # Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.run(host='0.0.0.0')

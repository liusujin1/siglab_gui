  function ax_scale(Action,In1,Owner_Str,Grph_Num,HAuxAxis)
% function ax_scale(Action,In1,Owner_Str,Grph_Num,HAuxAxis)
% Simple scale support for axis. Axis must use pixel position/size.
% Dick Benson, DSP Technology

  global HAX_Scale_;
  
  %define
    %Fig          = 1; 
    %Print_pb     = 2;
    %X_min        = 3;
    %X_max        = 4;
    %Y_min        = 5;
    %Y_max        = 6;
    %Owner_Axis   = 7; % owner axis handle (index)
    %Owner_Grph   = 8;
    %Aux_Axis     = 9; % auxilliary axis handle (e.g. coherence in vna)
  %end_define

  % ****************************************************
  %include
% vcol_h.m                                                   
%%%%%%%%%%%%%%%%%%%%%%%%end_include
  % **************************************************** 


  if strcmp(Action,'init')
  
      HAX_Scale_= zeros(1,9);
  
      % if the file vi_color.mat with startup colors exists,
      % use the colors in it rather than INIT_COLORc
      if exist('vi_color.mat') ==2
         load vi_color 
         my_color=stored_vi_colors;
      else
         my_color=[[0,0,.25098];[0,0.50196,0.50196];[0.75294,0.75294,0.75294];[1,1,0];[0,0,0];[0,0,1];[0,1,0];[0,1,1];[1,1,0];[1,0,0];[0.25098,0,0];[0,1,0];[0.75294,0.75294,0.75294];[1,1,1];[1,1,1];[0,0,0];[0,0,0];[0,0,0];[0,0,0];[1,1,0];[1,0,0];[.3,.3,.3];];
      end; 

  
       HAX_Scale_(7)  = In1;             % owner axis handle
       HAX_Scale_(8)  = Grph_Num;        % owner graph number 
       if nargin ==5
          HAX_Scale_(9) = HAuxAxis;
       else
          HAX_Scale_(9) = [];
       end;
       ppos                  = get(In1,'pos');
       vsize                 = 120;
       ypos=ppos(2)+ppos(4)-vsize;
       my_name='Set Scale';
       HAX_Scale_(1)=figure('numbertitle','off','resize','off','menu','none',...
                              'visible','off',...
                              'pos',[ppos(1),ypos, 220, vsize],... 
                              'color', my_color(2,:),... 
                              'name',my_name,'BackingStore','off',...
                              'userdata',Owner_Str); 
       
       xlim=get(In1,'Xlim');
       ylim=get(In1,'Ylim');
      
       if beyondv4
           set(HAX_Scale_(1),'WindowStyle','modal');
           scbh  = 'close(gcf)';
       else 
           modal(my_name);
           scbh  = [' modal(',['''',my_name,''''],'); close(gcf); drawnow;'];
       end;
       
       uicontrol(HAX_Scale_(1),'style','text','str','Min              Max',...
                 'pos',[45 95 155 16],'backgroundcolor',my_color(3,:));
       uicontrol(HAX_Scale_(1),'style','text','str','Y','pos',[10 70 30 16],...
                 'backgroundcolor',my_color(3,:)); 
       uicontrol(HAX_Scale_(1),'style','text','str','X','pos',[10 45 30 16],...
                 'backgroundcolor',my_color(3,:));
       
       HAX_Scale_(5)= uicontrol(HAX_Scale_(1),'style','edit','str',num2str(ylim(1)),...
                                    'pos',[45 70 75 16],'backgroundcolor',my_color(4,:));

       HAX_Scale_(6)=uicontrol(HAX_Scale_(1),'style','edit','str',num2str(ylim(2)),...
                                    'pos',[125 70 75 16],'backgroundcolor',my_color(4,:));
               
       HAX_Scale_(3)= uicontrol(HAX_Scale_(1),'style','edit','str',num2str(xlim(1)),...
                                    'pos',[45 45 75 16],'backgroundcolor',my_color(4,:));

       HAX_Scale_(4)=uicontrol(HAX_Scale_(1),'style','edit','str',num2str(xlim(2)),...
                                   'pos',[125 45 75 16],'backgroundcolor',my_color(4,:));
      

       HAX_Scale_(2) = uicontrol(HAX_Scale_(1),'style','pushbutton','str','Scale',...
                                        'pos' ,[25 10  55 20],...
                                        'callback',['ax_scale(''scale'')']);
     
       uicontrol(HAX_Scale_(1),'style','pushbutton','str','Cancel','pos',[140 10 55 20],...
                 'callback',scbh); 

       set(HAX_Scale_(1),'visible','on');

  elseif strcmp(Action,'scale') 
       xmin=s2n(get(HAX_Scale_(3),'str'));
       xmax=s2n(get(HAX_Scale_(4),'str'));
       ymin=s2n(get(HAX_Scale_(5),'str'));
       ymax=s2n(get(HAX_Scale_(6),'str'));
      
       % check if the numbers are good ...
       s=[];
       if isempty(xmin)
          s=[' X min. Only numerical values allowed.']; 
       elseif isempty(xmax) 
          s=[' X max. Only numerical values allowed.']; 
       elseif isempty(ymin) 
          s=[' Y min. Only numerical values allowed.']; 
       elseif isempty(ymax) 
          s=[' Y max. Only numerical values allowed.']; 
       elseif (xmin>xmax) 
          s=[' X min must be < X max.']; 
       elseif (ymin>ymax) 
          s=[' Y min must be < Y max.']; 
       else
          if beyondv4
             set(HAX_Scale_(1),'WindowStyle','normal');
          else 
             modal(get(HAX_Scale_(1),'name'));  % toggle modal
          end;
       
         
          Owner_Str=get(HAX_Scale_(1),'userdata');
          close(HAX_Scale_(1));                 % close this dialog for the print
          set(HAX_Scale_(7),'Ylim',[ymin ymax],'Xlim',[xmin xmax]); 
          
          if length(HAX_Scale_) >= 9
             set(HAX_Scale_(9),'Xlim',[xmin xmax]); 
          end;
          
          eval([Owner_Str,'(''set'',''ax_dat'',',int2str(HAX_Scale_(8)),');']);
          % report changes to owner for cursor area threshold update
       end;
     
       if ~isempty(s)
          s =['Input entry error ',s, ' Understand?'];
          uiyncf('Input Error',(get(gcf,'Position')),s,'  ','','');
       end;
  

     
  else % end of  print 
     disp([Action,' oops']);
  end; % main if then else construction (switchyard)
% end  function










